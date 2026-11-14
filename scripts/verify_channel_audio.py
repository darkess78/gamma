"""Finite real Core/Qwen speech through authenticated private HTTP consumers.

Two authored stream events, no microphone, browser automation, public sends or
human-hearing claim. HTTP receipts describe received WAV transport only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
import uuid
import zipfile

from growth_probe_support import Host


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def source_files(project):
    files=[p for p in (project/'src/gamma').rglob('*.py') if '__pycache__' not in p.parts]
    files.extend(project/'scripts'/name for name in (
        'verify_channel_audio.py','growth_probe_support.py','verify_growth_flow.py',
        'verify_practical_work.py','verify_coordination_foundation.py'))
    return {p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def child(project,state):
    # This observes the actual configured provider call without substituting a
    # result, holding it, changing its arguments or weakening source validation.
    from gamma.voice.tts import TTSService
    original=TTSService.synthesize
    lock=threading.Lock()
    def note(value):
        with lock:
            with (state/'actual-tts-calls.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(value)+'\n')
    def observed(self,text,*args,**kwargs):
        identifier=uuid.uuid4().hex
        note({'event':'start','id':identifier,'at':time.time(),
              'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'characters':len(text)})
        try:
            result=original(self,text,*args,**kwargs)
            data=Path(result.audio_path).read_bytes()
            note({'event':'finish','id':identifier,'at':time.time(),'ok':True,
                  'provider':str(result.provider),'bytes':len(data),'audio_sha256':hashlib.sha256(data).hexdigest()})
            return result
        except BaseException as error:
            note({'event':'finish','id':identifier,'at':time.time(),'ok':False,'error_type':type(error).__name__})
            raise
    TTSService.synthesize=observed
    from growth_probe_support import child as existing_child
    existing_child(project,state)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.8:27b')
    parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--profile',default='qwen_shana_dataset_xvec_soft')
    parser.add_argument('--child-state',type=Path)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('Run on the isolated Gamma candidate only.')
    if args.endpoint!='http://127.0.0.1:11434':
        raise RuntimeError('Use the existing local inference endpoint only.')
    if args.child_state:
        child(project,args.child_state);return
    root=project/'data/coordination/p7'/('actual-audio-'+uuid.uuid4().hex)
    root.mkdir(parents=True,mode=0o700)
    os.environ.update(SHANA_TTS_PROVIDER='qwen-tts',SHANA_TTS_PROFILE=args.profile,
                      SHANA_AUDIO_OUTPUT_DIR=str(root/'provider-audio'))
    before=source_files(project)
    archive=root/'executed-source.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in before:bundle.write(project/name,name)
    report={'kind':'p7_actual_checked_channel_audio_http','complete':False,
        'author':os.environ.get('USER','neety')+'@'+socket.gethostname(),
        'source_sha256':before,'source_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'model':args.model,'voice_profile':args.profile,'synthetic_authored_events':True,
        'physical_microphone':False,'browser_control':False,'human_hearing':False,'public_delivery':False,
        'receipt_meaning':'declared private HTTP consumer received WAV bytes; no media-played claim',
        'formats':[]}
    host=None;active=None;heartbeat=None;stop=threading.Event();started=time.monotonic()
    try:
        host=Host(project,root/'state',args.model,args.endpoint)
        host.child_script=Path(__file__)
        host.start();report['host_pid']=host.process.pid
        prefix='/api/coordination'
        consumer='checked-audio-proof'
        def post(path,**values):return host.request('POST',prefix+path,json={'audience':'owner_private',**values})
        def get(path,**values):return host.request('GET',prefix+path,params={'audience':'owner_private',**values})
        report['identity_before']=get('/personal')['identity_id']
        def keepalive():
            while not stop.wait(10):
                if active:
                    try:post('/stream/heartbeat',session_id=active,consumer_id=consumer)
                    except Exception:pass
        heartbeat=threading.Thread(target=keepalive,daemon=True);heartbeat.start()
        def provider_events():
            path=root/'state/actual-tts-calls.jsonl'
            return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        def active_calls(after):
            events=provider_events();finished={e['id'] for e in events if e['event']=='finish'}
            return [e for e in events if e['event']=='start' and e['at']>=after and e['id'] not in finished]
        for format_,plan,question,interrupt in [
            ('gameplay','Keep company during a relaxed OSRS skilling session.',
             'Shana, give two short sentences on why quiet OSRS skilling can be enjoyable.',False),
            ('coding','Build and test a disposable text parser together.',
             'Shana, give two short sentences: one about testing empty input, then one about testing mixed delimiters.',True)]:
            session=post('/stream/start',request_id=uuid.uuid4().hex,format=format_,broad_plan=plan,
                         duration_seconds=900,maximum_decisions=4)
            active=session['id'];scope={'session_id':active,'consumer_id':consumer}
            post('/stream/connect',**scope)
            assert not post('/channel-audio/connect',consumer_id=consumer)['listen_enabled']
            post('/channel-audio/enable',consumer_id=consumer,enabled=True)
            source=post('/stream/event',**scope,event_id=uuid.uuid4().hex,kind='chat',source='replay',
                        observed_at=time.time(),text=question,actor_alias='ExampleViewer')
            assert source['status']=='ready',source
            text=get('/stream/next',**scope)['output'];assert text and text['id']==source['output_id']
            post('/stream/ack',**scope,output_id=text['id'],generation=text['generation'],revision=text['revision'],
                 status='delivered',receipt_id=uuid.uuid4().hex)
            row={'format':format_,'source_output_id':text['id'],'checked_text':text['text'],
                 'received_wav':[],'text_receipt':'declared HTTP text delivered'}
            report['formats'].append(row);save(root/'report.json',report)
            render_started=time.time();stop_response=None;fetched=set()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(post,'/channel-audio/render',stream_output_id=text['id'],consumer_id=consumer)
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    item=get('/interaction/poll',consumer_id=consumer)['output']
                    if item and item['id'] not in fetched:
                        params={'audience':'owner_private','consumer_id':consumer,
                                'generation':item['generation'],'output_epoch':item['output_epoch']}
                        response=host.client.get(prefix+'/interaction/audio/'+item['id'],params=params)
                        response.raise_for_status();data=response.content
                        from gamma.coordination.interaction import validate_wave
                        duration=validate_wave(data,maximum_seconds=90)
                        (root/(format_+'-'+str(item['chunk_index'])+'.wav')).write_bytes(data)
                        row['received_wav'].append({'output_id':item['id'],'chunk_index':item['chunk_index'],
                            'chunk_count':item['chunk_count'],'speech_text':item['speech_text'],
                            'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'seconds':duration,
                            'provider_function_still_active':bool(active_calls(render_started))})
                        fetched.add(item['id'])
                        post('/interaction/ack',**{k:v for k,v in params.items()if k!='audience'},
                             output_id=item['id'],status='delivered')
                        save(root/'report.json',report)
                    if interrupt and fetched:
                        if active_calls(render_started):
                            stop_response=post('/channel-audio/interrupt',consumer_id=consumer)
                            row['stop_response_at']=time.time();break
                        if future.done():break
                    if future.done():
                        receipt=future.result()
                        if fetched==set(receipt.get('output_ids',[])):break
                    time.sleep(.05)
                receipt=future.result(timeout=180);row['render_receipt']=receipt
            assert row['received_wav'],'No configured Qwen speech was received for '+format_
            if interrupt:
                assert stop_response is not None,'No actual later provider call remained in flight after the first WAV.'
                assert receipt['status']=='cancelled',receipt
                events=provider_events();intervals=[]
                for event in events:
                    if event['event']=='start' and event['at']>=render_started:
                        end=next((e for e in events if e['event']=='finish' and e['id']==event['id']),None)
                        if end and event['at']<=row['stop_response_at']<end['at']:
                            intervals.append({'start':event['at'],'stop_response':row['stop_response_at'],
                                              'finish':end['at'],'provider':end.get('provider'),'ok':end.get('ok')})
                assert intervals,'Stop was not observed inside a real configured provider call.'
                row['actual_provider_interrupted_at_release']=intervals
                assert get('/interaction/poll',consumer_id=consumer)['output'] is None
                starts=sum(e['event']=='start'for e in events)
                post('/channel-audio/disconnect',consumer_id=consumer)
                assert not post('/channel-audio/connect',consumer_id=consumer)['listen_enabled']
                post('/channel-audio/enable',consumer_id=consumer,enabled=True)
                retry=post('/channel-audio/render',consumer_id=consumer,stream_output_id=text['id'])
                assert retry['status']!='generated',retry
                assert sum(e['event']=='start'for e in provider_events())==starts
                row['reconnect_no_replay']=True
            else:
                assert receipt['status']=='generated' and fetched==set(receipt['output_ids'])
            post('/channel-audio/disconnect',consumer_id=consumer)
            post('/stream/control',request_id=uuid.uuid4().hex,session_id=active,action='stop');active=None
            save(root/'report.json',report)
        report['identity_after']=get('/personal')['identity_id']
        assert report['identity_before']==report['identity_after']
        global_flags=get('/interaction/status',consumer_id=consumer)
        assert not global_flags['voice_enabled'] and not global_flags['speech_enabled']
        report['global_voice_flags_unchanged']=True
        report['actual_provider_events']=provider_events()
        report['source_after_sha256']=source_files(project)
        report['source_stable']=report['source_sha256']==report['source_after_sha256']
        assert report['source_stable'],'Executed Python changed; retain the nonfinal receipt.'
        report['complete']=True
    except BaseException as error:
        report['error_type']=type(error).__name__
        raise
    finally:
        stop.set()
        if heartbeat:heartbeat.join(timeout=15)
        if host:
            if active:
                try:post('/stream/control',request_id=uuid.uuid4().hex,session_id=active,action='stop')
                except Exception:pass
            host.stop();report['owned_host_absent']=host.process.poll() is not None
        database=root/'state/coordination.sqlite3'
        if database.exists():
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
                report['journal_counts']=dict(db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind').fetchall())
                assert report['journal_counts'].get('turn',0)==0,'A channel event became a fake owner turn.'
        report['seconds']=round(time.monotonic()-started,3);save(root/'report.json',report)
        print(json.dumps({'report':str(root.relative_to(project)/'report.json'),'complete':report['complete'],
                          'seconds':report['seconds'],'owned_host_absent':report.get('owned_host_absent')}))


if __name__=='__main__':main()
